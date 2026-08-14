#!/usr/bin/env python3
"""PDU cyclic publisher -- configures a raw PDU route via the pdu_router
plugin and sends a fixed payload on a fixed interval.

Demonstrates using a *plugin* from a node script, not just raw CAN/Ethernet
frames: PDUs (AUTOSAR-style protocol data units) only exist once the
pdu_router plugin is loaded on the gateway
(BOAT_NODE_PLUGINS=.../pdu_router.so). Unlike FrameNode's send_can(),
which sends one raw CAN frame straight through the gateway's core
FrameSink, PduNode.configure_route()/send() are delegated by the gateway
to that plugin's own gRPC service (PduService) -- without the plugin
loaded, configure_route() returns False and send() is a no-op.

What this node does, step by step (see boat.pdu_node.PduNode for the full
API this wraps):
  1. configure_route(): registers a PDU ID -> (transport, iface, CAN ID)
     routing rule with the plugin -- this is what turns a bare integer
     PDU ID into "send this as a CAN frame with ID <pdu_id> on vcan0", the
     same relationship a PDU database expresses for a whole fleet of
     messages at once (see boat.pdu_message_node.PduMessageNode for the
     database-driven equivalent -- this node does the same thing for one
     message, spelled out by hand, as the simplest possible example).
  2. send(): sends a fixed payload as that PDU, on the configured cycle.

Key lesson this node exists to demonstrate: plugin state lives in the
*gateway process*, not the client. A raw CAN sender (cyclic_can_sender.py)
has nothing to lose when the gateway restarts -- the bus is stateless, so
the very next send_can() just works again. A PDU route is different: it's
configuration the pdu_router plugin holds in memory, so a gateway restart
wipes it too, not just the gRPC connection. This node's main loop
re-registers the route whenever a send fails, not only reconnects --
retrying send() alone against a fresh gateway with no route configured
would silently do nothing forever.

Usage:
    python3 nodes/pdu_cyclic_publisher.py --iface vcan0 --pdu-id 0x100 \
        --data 0102030405060708 --cycle-ms 500

Requires the pdu_router plugin loaded on the target gateway, e.g.:
    BOAT_NODE_PLUGINS=<path>/pdu_router.so ./boat_gateway

Gateway address resolution / build_parser() convention -- see
cyclic_can_sender.py's docstring.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from boat.pdu_node import PduNode
from boat.v1 import pdu_pb2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BoAt PDU cyclic publisher node (pdu_router plugin example)")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--iface", default="vcan0", help="CAN interface to route the PDU onto")
    parser.add_argument("--pdu-id", default="0x100",
                         help="PDU ID, hex (0x..) or decimal -- also used as the CAN ID")
    parser.add_argument("--data", default="0102030405060708",
                         help="Payload as hex bytes, e.g. 0102030405060708 (empty = 0-byte PDU)")
    parser.add_argument("--cycle-ms", type=int, default=500, help="Send interval in milliseconds")
    return parser


def _configure_route(node: PduNode, pdu_id: int, iface: str) -> bool:
    return node.configure_route(
        pdu_id=pdu_id,
        transport=pdu_pb2.PDU_TRANSPORT_CAN,
        iface=iface,
        can_id=pdu_id,
    )


def main() -> None:
    args = build_parser().parse_args()

    pdu_id = int(args.pdu_id, 0)
    payload = bytes.fromhex(args.data) if args.data else b""

    stop = threading.Event()

    def shutdown(*_args) -> None:
        print("\n[pdu-cyclic-publisher] shutting down…")
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    node = PduNode(args.address)
    configured = _configure_route(node, pdu_id, args.iface)
    if not configured:
        print("[pdu-cyclic-publisher] WARNING: configure_route() failed -- is the "
              "pdu_router plugin loaded on this gateway? "
              "(BOAT_NODE_PLUGINS=<path>/pdu_router.so) -- will keep retrying "
              "every cycle.", file=sys.stderr)

    print(f"[pdu-cyclic-publisher] sending PDU 0x{pdu_id:X} on {args.iface} every "
          f"{args.cycle_ms} ms (data={payload.hex(':') or '(empty)'})")
    cycle_s = args.cycle_ms / 1000.0
    while not stop.is_set():
        cycle_start = time.monotonic()
        if not configured:
            configured = _configure_route(node, pdu_id, args.iface)
        ok = configured and node.send(pdu_id, payload)
        if not ok:
            # Either the route still isn't configured, or the send itself
            # failed -- most likely the gateway restarted, which wipes the
            # pdu_router plugin's routes along with the connection. A
            # fresh PduNode (fresh gRPC channel, same reasoning as
            # FrameNode._reconnect() in sdk/python/boat/frame_node.py) plus
            # re-registering the route is what a plain retry alone would
            # miss -- see this file's docstring.
            node = PduNode(args.address)
            configured = False
            print("[pdu-cyclic-publisher] send failed; will reconfigure and "
                  "retry next cycle", file=sys.stderr)
        # Deadline relative to when THIS cycle started, not to when send()
        # returned -- see cyclic_can_sender.py's docstring for why.
        deadline = cycle_start + cycle_s
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.05))

    sys.exit(0)


if __name__ == "__main__":
    main()
