#!/usr/bin/env python3
"""CAN request/responder -- replies to one CAN ID with a fixed response frame.

A minimal ECU-simulation building block: waits for --request-id on
--iface, sends --response-id/--response-data back. Useful for testing
anything that sends CAN requests (CanTp, PDU routing, diagnostic tools, ...)
without needing a full ECU simulation.

Usage:
    python3 nodes/can_request_responder.py --iface vcan0 \\
        --request-id 0x7E0 --response-id 0x7E8 --response-data 5001

Gateway address resolution -- same order as everywhere else in this repo:
--address flag > BOAT_HOST env var > localhost:50051. See
cyclic_can_sender.py's docstring for why --address defaults to None rather
than a hardcoded string.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from boat.frame_node import FrameNode


def main() -> None:
    parser = argparse.ArgumentParser(description="BoAt CAN request/responder node")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--iface", default="vcan0", help="CAN interface to listen/reply on")
    parser.add_argument("--request-id", default="0x7E0", help="CAN ID to react to, hex (0x..) or decimal")
    parser.add_argument("--response-id", default="0x7E8", help="CAN ID to reply with, hex (0x..) or decimal")
    parser.add_argument("--response-data", default="", help="Reply payload as hex bytes (empty = 0-byte frame)")
    args = parser.parse_args()

    request_id = int(args.request_id, 0)
    response_id = int(args.response_id, 0)
    response_data = bytes.fromhex(args.response_data) if args.response_data else b""

    node = FrameNode(args.address)

    def on_frame(frame) -> None:
        if frame.iface != args.iface or frame.can.can_id != request_id:
            return
        print(f"[responder] 0x{request_id:X} on {args.iface} -> replying "
              f"0x{response_id:X} {response_data.hex(':') or '(empty)'}")
        node.send_can(args.iface, response_id, response_data)

    node.subscribe(on_frame, bus_types=["CAN"])
    print(f"[responder] listening for 0x{request_id:X} on {args.iface}, will reply 0x{response_id:X}")

    import signal
    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())

    node.run()
    print("[responder] shutting down…")
    sys.exit(0)


if __name__ == "__main__":
    main()
