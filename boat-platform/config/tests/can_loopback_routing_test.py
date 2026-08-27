#!/usr/bin/env python3
# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

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

Pure gateway API, no raw SocketCAN reads: subscribes to both --tx-iface
and --rx-iface via FrameService.SubscribeFrames, injects each test frame
via FrameService.SendFrame on --tx-iface, and compares what comes back on
each subscription -- including each frame's own `timestamp_ns`, giving::

    routing_time = frame_on_rx_iface.timestamp_ns - frame_on_tx_iface.timestamp_ns

An earlier version of this test read raw SocketCAN sockets directly
instead, specifically to keep gRPC transport latency out of the
measurement. That's no longer necessary: `timestamp_ns` is captured
*server-side*, before the Frame is ever serialized for gRPC, so how long
it then takes a client to receive and deserialize the message doesn't
affect the *value* recorded -- only when the client learns it. This
version is simpler (no python-can dependency, one subscription mechanism
instead of two), and it now also genuinely exercises *both* halves of the
gateway's job: the earlier raw-socket design's --rx-iface observation
bypassed the gateway's own RX thread -> registry dispatch ->
SubscribeFrames path entirely (can0/can1 being bridged at the transceiver
level meant the frame got there regardless of whether the gateway's own
software was even involved); subscribing on --rx-iface here does not.

Making this fair for the --tx-iface side needed one real fix, not just a
different observation method: nothing on the send path (FrameService.
SendFrame, `boat frame send`, FrameNode.send_can() -- checked all three)
ever sets `timestamp_ns` on the outgoing frame, and the registry's
self-sent echo used to just carry that value through unmodified --
meaning it was reliably 0 for every send, useless for a `TS_rx - TS_tx`
comparison. Fixed at the registry level (`CanBusRegistry::SendFrame()`/
`SendFrameAll()`, `src/hil/can_bus_registry.cpp`, and the equivalent
`EthernetBusRegistry` methods for consistency): the self-sent echo now
carries a timestamp captured fresh right after the write, the same
`clock_gettime(CLOCK_REALTIME)` call `SocketCanDriver::ReadFrame()` uses
for genuine wire RX -- not the client-supplied (always 0) value.

Verifies three things end to end:
  - payload: every received frame's payload matches exactly what was sent
  - routing time: rx.timestamp_ns - tx.timestamp_ns stays under
    --max-latency-ms (default 5.0ms -- deliberately loose; this is a
    methodology check, not a performance benchmark) for every frame
  - routing behavior: exactly one receive per send, no drops

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
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))

from boat.frame_node import FrameNode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAN loopback routing HIL test")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--tx-iface", default="can0", help="Interface to inject on")
    parser.add_argument("--rx-iface", default="can1", help="Interface to observe arrival on")
    parser.add_argument("--can-id", default="0x7C1", help="Test CAN ID, hex (0x..) or decimal")
    parser.add_argument("--count", type=int, default=100, help="Number of test frames to send")
    parser.add_argument("--max-latency-ms", type=float, default=5.0,
                         help="Max allowed routing time per frame -- deliberately loose by "
                              "default; this test is about a correct approach, not a tight bound")
    parser.add_argument("--interval-ms", type=float, default=5.0,
                         help="Delay between sends (paces the bus, keeps frames distinguishable in time)")
    parser.add_argument("--per-frame-timeout-s", type=float, default=1.0,
                         help="How long to wait for each side's observation before counting a drop")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    can_id = int(args.can_id, 0)

    node = FrameNode(args.address)
    lock = threading.Lock()
    pending: dict[bytes, dict] = {}   # payload -> {"tx": frame|None, "rx": frame|None, "event": Event}

    def on_frame(frame) -> None:
        if frame.can.can_id != can_id or frame.iface not in (args.tx_iface, args.rx_iface):
            return
        payload = bytes(frame.payload)
        side = "tx" if frame.iface == args.tx_iface else "rx"
        with lock:
            entry = pending.get(payload)
            if entry is None:
                return  # not (or no longer) awaited -- unrelated/stray traffic
            entry[side] = frame
            if entry["tx"] is not None and entry["rx"] is not None:
                entry["event"].set()

    node.subscribe(on_frame, bus_types=["CAN"])
    time.sleep(0.3)  # let the subscribe stream actually establish before sending

    results: list[tuple[int, Optional[float]]] = []
    failures: list[str] = []

    print(f"[can-loopback-routing] sending {args.count} frames: "
          f"{args.tx_iface} -> (shared bus) -> {args.rx_iface}, "
          f"id=0x{can_id:X}, max_routing_time={args.max_latency_ms}ms")

    for seq in range(args.count):
        payload = seq.to_bytes(2, "big") + b"\xA5" * 6  # 8 bytes, sequence-tagged
        entry = {"tx": None, "rx": None, "event": threading.Event()}
        with lock:
            pending[payload] = entry

        node.send_can(args.tx_iface, can_id, payload)
        got_both = entry["event"].wait(timeout=args.per_frame_timeout_s)

        with lock:
            del pending[payload]

        if not got_both:
            missing = "tx" if entry["tx"] is None else "rx"
            missing_iface = args.tx_iface if missing == "tx" else args.rx_iface
            results.append((seq, None))
            failures.append(f"seq {seq}: no matching frame observed on {missing_iface} "
                             f"within {args.per_frame_timeout_s}s")
        else:
            tx_frame, rx_frame = entry["tx"], entry["rx"]
            routing_ms = (rx_frame.timestamp_ns - tx_frame.timestamp_ns) / 1e6
            results.append((seq, routing_ms))
            if bytes(rx_frame.payload) != payload:
                failures.append(f"seq {seq}: payload mismatch (unexpected -- matched by payload)")
            if tx_frame.timestamp_ns == 0 or rx_frame.timestamp_ns == 0:
                failures.append(f"seq {seq}: timestamp_ns still 0 on one side "
                                 f"(tx={tx_frame.timestamp_ns}, rx={rx_frame.timestamp_ns}) "
                                 "-- registry send-side timestamp fix not present?")
            elif routing_ms > args.max_latency_ms:
                failures.append(f"seq {seq}: routing time {routing_ms:.3f}ms exceeds "
                                 f"{args.max_latency_ms}ms")
            if routing_ms < 0:
                failures.append(f"seq {seq}: negative routing time {routing_ms:.3f}ms "
                                 f"(rx observed before tx? clock/ordering issue)")

        time.sleep(args.interval_ms / 1000.0)

    node.stop()

    received = [r for r in results if r[1] is not None]
    times = [r[1] for r in received]

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
