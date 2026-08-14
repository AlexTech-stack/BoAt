#!/usr/bin/env python3
"""CAN-TP echo responder -- echoes back any ISO-TP (ISO 15765-2) message it
receives, verbatim, on the reverse addressing.

Demonstrates using a *plugin* from a node script: multi-frame CAN
segmentation/reassembly only exists once the can_tp plugin is loaded on
the gateway (BOAT_NODE_PLUGINS=.../can_tp.so?{"iface":"..."}). Unlike
FrameNode's send_can(), which sends one raw CAN frame straight through the
gateway's core FrameSink, CanTpHandle.configure()/send()/subscribe() are
delegated by the gateway to that plugin's own gRPC service (CanTpService).
Segmenting a payload larger than one CAN frame into First Frame +
Consecutive Frames, waiting for Flow Control, N_Bs/N_Cr timeouts -- all of
that happens inside the plugin; this node only ever sees whole,
already-reassembled payloads in, and hands whole payloads back out.

Compare with can_request_responder.py: same request/responder shape (one
message in, one message out), but that one works directly in raw CAN
frames with no plugin involved, capped at 8 (or 64, for CAN FD) payload
bytes. This node does the same job over ISO-TP, so it can carry payloads
far longer than that -- what real UDS/diagnostic services need in
practice.

What this node does, step by step (see boat.can_tp.CanTpHandle for the
full API this wraps):
  1. configure(): registers one N-SDU connection with the plugin -- the
     mapping from an arbitrary session id (nsdu_id) to a pair of CAN IDs
     (source_addr = what this node sends as, target_addr = what it
     listens for) the plugin segments/reassembles on.
  2. subscribe(): streams fully reassembled RX payloads for that session.
  3. send(): on each one received, sends the same bytes back through the
     plugin's own segmentation on the same session, so an echo longer
     than one frame comes back correctly segmented too.

Key lesson this node exists to demonstrate (same one pdu_cyclic_publisher.py
makes for PDU routes): plugin state -- the configured N-SDU session here --
lives in the *gateway process*, not the client. A gateway restart wipes it
along with the connection, so this node's reconnect loop below always
calls configure() again before it resumes listening, not just reconnects
the stream.

Usage:
    python3 nodes/can_tp_echo_responder.py --iface vcan0 \
        --nsdu-id 1 --source-addr 0x7E8 --target-addr 0x7E0

Requires the can_tp plugin loaded for --iface on the target gateway, e.g.:
    BOAT_NODE_PLUGINS=<path>/can_tp.so?{"iface":"vcan0"} ./boat_gateway

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
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from boat.can_tp import CanTpHandle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BoAt CAN-TP echo responder node (can_tp plugin example)")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--iface", default="vcan0",
                         help="CAN interface the can_tp plugin instance is on")
    parser.add_argument("--nsdu-id", type=int, default=1,
                         help="N-SDU session identifier (arbitrary, non-zero)")
    parser.add_argument("--source-addr", default="0x7E8",
                         help="CAN ID this node sends as, hex (0x..) or decimal")
    parser.add_argument("--target-addr", default="0x7E0",
                         help="CAN ID this node listens for, hex (0x..) or decimal")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    nsdu_id = args.nsdu_id
    source_addr = int(args.source_addr, 0)
    target_addr = int(args.target_addr, 0)

    stop = threading.Event()
    current_stream = [None]  # boxed so shutdown() can cancel whatever's live

    def shutdown(*_args) -> None:
        print("\n[can-tp-echo] shutting down…")
        stop.set()
        if current_stream[0] is not None:
            current_stream[0].cancel()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    backoff = 1.0
    max_backoff = 10.0
    while not stop.is_set():
        can_tp = CanTpHandle(args.address)
        try:
            # Unlike PduNode.configure_route() (sdk/python/boat/pdu_node.py),
            # CanTpHandle.configure()/subscribe()/send() do NOT catch
            # grpc.RpcError internally -- a gateway that's down or mid-
            # restart makes this raise, not just return False. Caught here
            # explicitly so a transient failure retries like everything
            # else in this loop instead of crashing the node outright.
            configured = can_tp.configure(nsdu_id=nsdu_id, source_addr=source_addr,
                                           target_addr=target_addr, iface=args.iface)
            configure_error: Optional[Exception] = None
        except Exception as e:
            configured = False
            configure_error = e

        if not configured:
            # configure_error's own message already says exactly what went
            # wrong -- e.g. grpc.RpcError NOT_FOUND "no CanTp plugin loaded
            # for iface 'vcan0'" when the plugin genuinely isn't loaded, or
            # UNAVAILABLE "Connection refused" when the gateway itself is
            # unreachable. Printed verbatim rather than guessed at, plus
            # the BOAT_NODE_PLUGINS hint for the common case.
            detail = f" ({configure_error})" if configure_error is not None else " (returned false)"
            print(f"[can-tp-echo] configure() failed{detail} -- is the can_tp "
                  f'plugin loaded for {args.iface}? (BOAT_NODE_PLUGINS=<path>/'
                  f'can_tp.so?{{"iface":"{args.iface}"}}) -- retrying in '
                  f"{backoff:.0f}s...", file=sys.stderr)
            if stop.wait(backoff):
                break
            backoff = min(backoff * 2, max_backoff)
            continue

        print(f"[can-tp-echo] nsdu_id={nsdu_id} listening as 0x{source_addr:X}, "
              f"echoing anything received from 0x{target_addr:X} on {args.iface}")
        connected_at = time.monotonic()
        try:
            stream = can_tp.subscribe(nsdu_ids=[nsdu_id], iface=args.iface)
            current_stream[0] = stream
            for event in stream:
                if stop.is_set():
                    break
                print(f"[can-tp-echo] received {len(event.data)} bytes: "
                      f"{event.data.hex(':') or '(empty)'} -- echoing back")
                can_tp.send(nsdu_id, event.data, iface=args.iface)
        except Exception as e:
            if stop.is_set():
                break
            print(f"[can-tp-echo] stream failed ({e}); reconfiguring and "
                  f"reconnecting in {backoff:.0f}s... (a gateway restart wipes "
                  "the plugin's session state too, not just the connection -- "
                  "see this file's docstring)", file=sys.stderr)
        finally:
            current_stream[0] = None

        if stop.is_set():
            break
        if time.monotonic() - connected_at > 5.0:
            backoff = 1.0  # was connected a while -- don't inherit a stale delay
        if stop.wait(backoff):
            break
        backoff = min(backoff * 2, max_backoff)

    sys.exit(0)


if __name__ == "__main__":
    main()
