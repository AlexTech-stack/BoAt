#!/usr/bin/env python3
"""CAN-TP trigger sender -- on a plain CAN trigger frame, sends a fresh
incrementing-byte payload through the can_tp plugin's own segmentation,
so a human with nothing but `cansend`/`candump` can watch (and drive) a
real multi-frame ISO-TP exchange by hand.

Demonstrates using a *plugin* from a node script: multi-frame CAN
segmentation only exists once the can_tp plugin is loaded on the gateway
(BOAT_NODE_PLUGINS=.../can_tp.so?{"iface":"..."}). Unlike FrameNode's
send_can(), which sends one raw CAN frame straight through the gateway's
core FrameSink, CanTpHandle.configure()/send() are delegated by the
gateway to that plugin's own gRPC service (CanTpService) -- segmenting a
payload larger than one CAN frame into First Frame + Consecutive Frames,
and waiting for Flow Control in between, all happens inside the plugin;
this node only ever calls send() once per trigger and lets it handle the
rest.

This replaces an earlier version of this node that tried to *echo back*
whatever ISO-TP message it received. That doesn't actually work for
manual testing: receiving a multi-frame message requires being a full
ISO-TP requester yourself (send First Frame, then send further
Consecutive Frames *in response to* the plugin's Flow Control) -- not
something a single `cansend` can do. A bare `cansend` of what looks like
a First Frame just makes the plugin emit Flow Control and then wait
forever for Consecutive Frames nobody sends, so nothing ever reassembles
and nothing was ever going to be echoed back. This version flips the
direction: the plugin does the (automatic) segmenting on the *send* side,
where a human only has to supply Flow Control by hand (or let a real
ISO-TP stack like `isotprecv` do it) -- much easier to actually drive
frame by frame from a terminal.

What this node does, step by step:
  1. FrameNode.subscribe() listens for a plain CAN trigger frame on
     --trigger-id (no plugin involved for this side -- it's an ordinary
     CAN frame, not ISO-TP).
  2. On one, the trigger frame's first payload byte is the length (0-255)
     of the message to generate; an empty trigger payload falls back to
     --default-length. The payload itself is simply incrementing bytes
     starting at 0x00 (wrapping at 0x100), e.g. length 10 ->
     00 01 02 03 04 05 06 07 08 09 -- easy to eyeball frame-by-frame on
     the wire and notice a dropped/duplicated/reordered byte.
  3. CanTpHandle.send() sends that payload as nsdu_id, addressed as
     --source-addr (what the FF/CF frames go out as) -- the plugin
     handles First Frame + waiting for Flow Control + Consecutive Frames
     from there. Flow Control is expected back on --target-addr, same as
     any N-SDU session (see CanTpHandle.configure()'s docstring).

Example, matching this node's own defaults (trigger 0x111, sends as
0x200, expects Flow Control on 0x201):
    cansend vcan0 111#0A                    # trigger: send 10 bytes
    # wire: 0x200  10 0A 00 01 02 03 04 05  (First Frame, length 10)
    cansend vcan0 201#300000CCCCCCCCCC       # Flow Control: CTS, BS=0, STmin=0
    # wire: 0x200  21 06 07 08 09 CC CC CC  (Consecutive Frame 1, padded)

Key lesson this node exists to demonstrate (same one pdu_cyclic_publisher.py
makes for PDU routes): plugin state -- the configured N-SDU session here --
lives in the *gateway process*, not the client. A gateway restart wipes it
along with the connection, so this node re-configures the session (not
just reconnects) whenever a send fails, lazily on the next trigger rather
than in a background loop, since sends only ever happen in response to one.

Usage:
    python3 nodes/can_tp_trigger_sender.py --iface vcan0 \
        --trigger-id 0x111 --nsdu-id 1 --source-addr 0x200 --target-addr 0x201

Requires the can_tp plugin loaded for --iface on the target gateway, e.g.:
    BOAT_NODE_PLUGINS=<path>/can_tp.so?{"iface":"vcan0"} ./boat_gateway

Gateway address resolution / build_parser() convention -- see
cyclic_can_sender.py's docstring.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))

from boat.can_tp import CanTpHandle
from boat.frame_node import FrameNode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="BoAt CAN-TP trigger sender node (can_tp plugin example)")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--iface", default="vcan0",
                         help="CAN interface the can_tp plugin instance is on")
    parser.add_argument("--trigger-id", default="0x111",
                         help="Plain CAN ID that triggers a send, hex (0x..) or decimal")
    parser.add_argument("--default-length", type=int, default=8,
                         help="Bytes to generate when the trigger frame's payload is empty")
    parser.add_argument("--nsdu-id", type=int, default=1,
                         help="N-SDU session identifier (arbitrary, non-zero)")
    parser.add_argument("--source-addr", default="0x200",
                         help="CAN ID this node sends the generated message as, hex (0x..) or decimal")
    parser.add_argument("--target-addr", default="0x201",
                         help="CAN ID this node expects Flow Control on, hex (0x..) or decimal")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    trigger_id = int(args.trigger_id, 0)
    nsdu_id = args.nsdu_id
    source_addr = int(args.source_addr, 0)
    target_addr = int(args.target_addr, 0)

    # Mutable box, not local vars, since on_frame() (a closure) needs to
    # both read and replace these across calls.
    state = {"can_tp": CanTpHandle(args.address), "configured": False}

    def ensure_configured() -> bool:
        """Configures the N-SDU session if it isn't already known-good.
        Called lazily -- once at startup (best-effort; a gateway/plugin
        that isn't up yet just gets retried on the first trigger, same as
        every failure after) and again on every trigger while not
        configured. On failure, replaces the CanTpHandle with a fresh one
        too (not just retries the same one) -- same reasoning as
        FrameNode._reconnect() (sdk/python/boat/frame_node.py): a channel
        with a failure history can stay stuck behind grpc-python's own
        reconnect backoff well after the gateway is reachable again."""
        if state["configured"]:
            return True
        try:
            ok = state["can_tp"].configure(nsdu_id=nsdu_id, source_addr=source_addr,
                                            target_addr=target_addr, iface=args.iface)
        except Exception as e:
            print(f"[can-tp-trigger-sender] configure() failed ({e}) -- is the "
                  f'can_tp plugin loaded for {args.iface}? (BOAT_NODE_PLUGINS='
                  f'<path>/can_tp.so?{{"iface":"{args.iface}"}})', file=sys.stderr)
            ok = False
        if not ok:
            state["can_tp"] = CanTpHandle(args.address)
        state["configured"] = ok
        return ok

    ensure_configured()

    node = FrameNode(args.address)

    def on_trigger(frame) -> None:
        if frame.iface != args.iface or frame.can.can_id != trigger_id:
            return
        length = frame.payload[0] if frame.payload else args.default_length
        if length == 0:
            print("[can-tp-trigger-sender] trigger requested 0 bytes -- nothing to send")
            return
        payload = bytes(i % 256 for i in range(length))
        print(f"[can-tp-trigger-sender] trigger 0x{trigger_id:X} on {args.iface} -> "
              f"sending {length} incrementing bytes as nsdu_id={nsdu_id}, "
              f"0x{source_addr:X} (Flow Control expected on 0x{target_addr:X})")

        # Up to two attempts: state["configured"] can be stale-true right
        # after a gateway restart -- nothing invalidates it just because
        # the gateway went away and came back, only an actual failed
        # configure()/send() call does (see ensure_configured()'s
        # docstring). Without a retry here, the *first* trigger after any
        # restart would send() against a session the plugin no longer has
        # (FAILED_PRECONDITION), get dropped, and only the *next* trigger
        # would actually go out -- a real gap, not just a hypothetical
        # one, caught by testing this exact scenario on real hardware.
        for attempt in range(2):
            if not ensure_configured():
                print("[can-tp-trigger-sender] not configured -- dropping this "
                      "trigger, will retry configuring on the next one",
                      file=sys.stderr)
                return
            try:
                if not state["can_tp"].send(nsdu_id, payload, iface=args.iface):
                    print("[can-tp-trigger-sender] send() reported failure",
                          file=sys.stderr)
                return
            except Exception as e:
                state["can_tp"] = CanTpHandle(args.address)
                state["configured"] = False
                if attempt == 0:
                    print(f"[can-tp-trigger-sender] send() raised ({e}); "
                          "reconfiguring and retrying this trigger once...",
                          file=sys.stderr)
                else:
                    print(f"[can-tp-trigger-sender] send() raised again ({e}); "
                          "giving up on this trigger, will reconfigure on the "
                          "next one", file=sys.stderr)

    node.subscribe(on_trigger, bus_types=["CAN"])
    print(f"[can-tp-trigger-sender] listening for trigger 0x{trigger_id:X} on "
          f"{args.iface}; will send nsdu_id={nsdu_id} as 0x{source_addr:X} "
          f"(Flow Control expected on 0x{target_addr:X})")

    signal.signal(signal.SIGINT, lambda *_: node.stop())
    signal.signal(signal.SIGTERM, lambda *_: node.stop())

    node.run()
    print("[can-tp-trigger-sender] shutting down…")
    sys.exit(0)


if __name__ == "__main__":
    main()
