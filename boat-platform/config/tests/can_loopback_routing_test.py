#!/usr/bin/env python3
"""CAN loopback routing test -- verifies the routing time of a "gateway
DUT" that relays every frame from can0 to can1 and vice versa, using two
physical CAN interfaces wired onto one shared bus as a DUT-free stand-in
(a CAN bus is broadcast, so anything either transceiver puts on the wire
is seen by both -- no software forwarding is actually required for the
frame to physically arrive, which is exactly what makes this a convenient
harness for shaking out the *test methodology* against real hardware
before pointing it at a DUT that has to do real work). Intended as a
`boat test run` manifest test entry (see manifest_can_loopback.json /
env_can_loopback.json), but also runs standalone.

Injects --count frames through the gateway's FrameService.SendFrame on
--tx-iface (so the gateway's own API is genuinely exercised, not
bypassed), and measures routing time as::

    routing_time = TS_message_on_rx_iface - TS_message_on_tx_iface

with **both** timestamps taken from raw SocketCAN reads (python-can), not
from anything gRPC-related. --tx-iface has CAN_RAW_LOOPBACK enabled (the
"ECHO" interface flag; verified with a real candump/cansend round trip
before relying on it here, not just assumed from the flag's presence), so
the gateway's own outbound write is independently observable on
--tx-iface itself with a genuine kernel timestamp -- not a local Python
timestamp guessed to be "when the write must have happened". This
deliberately excludes the gRPC client call's
own latency from the measurement: that call's time is spent *before* the
frame ever reaches the wire, so it's the API accepting the request, not
the gateway "routing" anything -- confirmed by tracing the actual send
path (FrameService.SendFrame -> FrameSink::Publish ->
CanBusRegistry::SendFrame -> HilBridge::SendFrame ->
driver_->WriteFrame()), which is fully synchronous within the gRPC
handler's own thread. Receive is equally synchronous: each CAN interface
has its own dedicated blocking-read thread that dispatches immediately on
arrival. Neither path is gated by the simulation tick (BOAT_NODE_TICK_MS,
default 1ms) -- the tick only drives plugin on_tick() callbacks (e.g.
CanTp's STmin pacing), not raw FrameService CAN send/receive -- so
sub-tick, sub-millisecond measurement of the gateway's own internal path
is architecturally sound; the tick is not a floor here.

Verifies three things end to end:
  - payload: every received frame's payload matches exactly what was sent
  - routing time: TS_rx - TS_tx stays under --max-latency-ms (default
    5.0ms -- deliberately loose; this is a methodology check, not a
    performance benchmark: point of this first version is a *correct
    approach*, not a tight bound) for every frame
  - routing behavior: exactly one receive per send, in order, no drops

Known simplification: a genuine duplicate delivery of a frame already
matched to an earlier sequence number isn't specifically flagged as a
"duplicate" -- it just fails to match whatever sequence number is
currently being awaited and is silently ignored (bounded by each frame's
own timeout window). Drops and payload corruption are still caught
reliably; true duplicate *detection* would need a longer post-hoc drain,
not done here.

Usage:
    python3 can_loopback_routing_test.py --address localhost:50067 \
        --tx-iface can0 --rx-iface can1 --count 100 --max-latency-ms 5.0

Exit code 0 = pass, 1 = fail -- matches `boat test run`'s subprocess
returncode contract (see sdk/python/boat/test/runner.py's
_run_single_test()).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))

import can  # python-can

from boat.frame_node import FrameNode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAN loopback routing HIL test")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--tx-iface", default="can0",
                         help="Interface to inject on (via the gateway's own API) -- must have "
                              "CAN_RAW_LOOPBACK enabled so its own outbound write is observable")
    parser.add_argument("--rx-iface", default="can1", help="Interface to observe arrival on")
    parser.add_argument("--can-id", default="0x7C1", help="Test CAN ID, hex (0x..) or decimal")
    parser.add_argument("--count", type=int, default=100, help="Number of test frames to send")
    parser.add_argument("--max-latency-ms", type=float, default=5.0,
                         help="Max allowed TS_rx - TS_tx routing time per frame -- deliberately "
                              "loose by default; this test is about a correct approach, not a "
                              "tight performance bound")
    parser.add_argument("--interval-ms", type=float, default=5.0,
                         help="Delay between sends (paces the bus, keeps frames distinguishable in time)")
    parser.add_argument("--per-frame-timeout-ms", type=float, default=100.0,
                         help="How long to wait for each side's observation before counting a drop")
    return parser


def _wait_for_match(bus: "can.BusABC", can_id: int, payload: bytes, deadline: float) -> Optional["can.Message"]:
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        msg = bus.recv(timeout=remaining)
        if msg is None:
            return None
        if msg.arbitration_id == can_id and bytes(msg.data) == payload:
            return msg
        # Not our frame (unrelated bus traffic, or a stray late arrival
        # from an earlier sequence number) -- keep waiting within window.


def main() -> None:
    args = build_parser().parse_args()
    can_id = int(args.can_id, 0)

    node = FrameNode(args.address)
    bus_tx = can.interface.Bus(channel=args.tx_iface, interface="socketcan")  # loopback observer
    bus_rx = can.interface.Bus(channel=args.rx_iface, interface="socketcan")  # arrival observer
    for b in (bus_tx, bus_rx):
        while b.recv(timeout=0) is not None:
            pass  # drain anything already queued before starting

    results: list[tuple[int, bytes, Optional[bytes], Optional[float]]] = []
    failures: list[str] = []

    print(f"[can-loopback-routing] sending {args.count} frames: "
          f"{args.tx_iface} -> (shared bus) -> {args.rx_iface}, "
          f"id=0x{can_id:X}, max_routing_time={args.max_latency_ms}ms")

    for seq in range(args.count):
        payload = seq.to_bytes(2, "big") + b"\xA5" * 6  # 8 bytes, sequence-tagged
        deadline = time.time() + (args.per_frame_timeout_ms / 1000.0)

        node.send_can(args.tx_iface, can_id, payload)

        msg_tx = _wait_for_match(bus_tx, can_id, payload, deadline)
        msg_rx = _wait_for_match(bus_rx, can_id, payload, deadline)

        if msg_tx is None or msg_rx is None:
            results.append((seq, payload, None, None))
            side = "tx" if msg_tx is None else "rx"
            failures.append(f"seq {seq}: no matching frame observed on "
                             f"{args.tx_iface if side == 'tx' else args.rx_iface} "
                             f"within {args.per_frame_timeout_ms}ms")
        else:
            routing_ms = (msg_rx.timestamp - msg_tx.timestamp) * 1000.0
            recv_payload = bytes(msg_rx.data)
            results.append((seq, payload, recv_payload, routing_ms))
            if recv_payload != payload:
                failures.append(f"seq {seq}: payload mismatch (sent {payload.hex()}, "
                                 f"got {recv_payload.hex()})")
            if routing_ms > args.max_latency_ms:
                failures.append(f"seq {seq}: routing time {routing_ms:.3f}ms exceeds "
                                 f"{args.max_latency_ms}ms")
            if routing_ms < 0:
                failures.append(f"seq {seq}: negative routing time {routing_ms:.3f}ms "
                                 f"(rx observed before tx? clock/ordering issue)")

        time.sleep(args.interval_ms / 1000.0)

    bus_tx.shutdown()
    bus_rx.shutdown()

    received = [r for r in results if r[3] is not None]
    times = [r[3] for r in received]

    print(f"[can-loopback-routing] {len(received)}/{args.count} frames received "
          f"({args.count - len(received)} dropped)")
    if times:
        times.sort()
        mean = sum(times) / len(times)
        p99 = times[int(len(times) * 0.99)] if len(times) >= 100 else times[-1]
        print(f"[can-loopback-routing] routing time: min={times[0]:.3f}ms "
              f"max={times[-1]:.3f}ms mean={mean:.3f}ms p99={p99:.3f}ms")

    if failures:
        print(f"[can-loopback-routing] FAIL -- {len(failures)} issue(s):", file=sys.stderr)
        for f in failures[:20]:
            print(f"  - {f}", file=sys.stderr)
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more", file=sys.stderr)
        sys.exit(1)

    print("[can-loopback-routing] PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
