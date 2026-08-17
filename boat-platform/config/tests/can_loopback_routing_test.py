#!/usr/bin/env python3
"""CAN loopback routing test -- verifies the gateway's own send-to-wire-to-
receive frame path using two physical CAN interfaces wired onto one shared
bus (can0/can1 bridged together at the transceiver level, not a software
routing feature -- a CAN bus is broadcast, so anything either transceiver
puts on the wire is seen by both). Intended as a `boat test run` manifest
test entry (see manifest_can_loopback.json / env_can_loopback.json), but
also runs standalone.

Sends --count frames through the gateway's FrameService.SendFrame on
--tx-iface, and independently observes their arrival on --rx-iface via a
raw SocketCAN read (python-can), *not* a second gRPC round trip -- so the
receive-side timestamp isn't itself inflated by gRPC/Python client
overhead the way a SubscribeFrames-based measurement would be. Verifies
three things end to end:
  - payload: every received frame's payload matches exactly what was sent
  - routing time: send-call-to-wire-arrival latency stays under
    --max-latency-ms (default 1.0ms) for every frame
  - routing behavior: exactly one receive per send, in order, no drops

Caveat on the latency measurement, since the default 1ms bar is tight:
the *send* timestamp is taken immediately before the gRPC SendFrame call,
so measured latency includes that call's own gRPC/Python overhead, not
just the gateway's internal dispatch. That internal dispatch was
confirmed event-driven, not gated by the simulation tick -- each CAN
interface gets its own dedicated blocking-read RX thread
(src/hil/hil_bridge.cpp) that dispatches to subscribers immediately on
each frame, so the C++ core itself is not expected to be the bottleneck
if this ever fails the bar; the gRPC client call is the more likely
contributor. The *receive* timestamp is python-can's Message.timestamp
(the kernel's own SocketCAN receive timestamp, wall-clock based) matched
against time.time() on the send side for the same clock domain -- about
as fair a measurement as practical without instrumenting the gateway's
internals directly.

Known simplification: a genuine duplicate delivery of a frame already
matched to an earlier sequence number isn't specifically flagged as a
"duplicate" -- it just fails to match whatever sequence number is
currently being awaited and is silently ignored (bounded by each frame's
own timeout window). Drops and payload corruption are still caught
reliably; true duplicate *detection* (as opposed to just not corrupting
the pass/fail verdict) would need a longer post-hoc drain, not done here.

Usage:
    python3 can_loopback_routing_test.py --address localhost:50067 \
        --tx-iface can0 --rx-iface can1 --count 100 --max-latency-ms 1.0

Exit code 0 = pass, 1 = fail -- matches `boat test run`'s subprocess
returncode contract (see sdk/python/boat/test/runner.py's
_run_single_test()).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))

import can  # python-can

from boat.frame_node import FrameNode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CAN loopback routing HIL test")
    parser.add_argument("--address", default=None,
                         help="Gateway address (default: BOAT_HOST env var, then localhost:50051)")
    parser.add_argument("--tx-iface", default="can0", help="Interface to send through (via the gateway)")
    parser.add_argument("--rx-iface", default="can1", help="Interface to observe arrivals on (raw SocketCAN)")
    parser.add_argument("--can-id", default="0x7C1", help="Test CAN ID, hex (0x..) or decimal")
    parser.add_argument("--count", type=int, default=100, help="Number of test frames to send")
    parser.add_argument("--max-latency-ms", type=float, default=1.0,
                         help="Max allowed send-to-wire-arrival latency per frame")
    parser.add_argument("--interval-ms", type=float, default=5.0,
                         help="Delay between sends (paces the bus, keeps frames distinguishable in time)")
    parser.add_argument("--per-frame-timeout-ms", type=float, default=100.0,
                         help="How long to wait for each frame's arrival before counting it as dropped")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    can_id = int(args.can_id, 0)

    node = FrameNode(args.address)
    bus = can.interface.Bus(channel=args.rx_iface, interface="socketcan")
    # Drain anything already queued (stale traffic, prior runs) before starting.
    while bus.recv(timeout=0) is not None:
        pass

    results: list[tuple[int, bytes, "bytes | None", "float | None"]] = []
    failures: list[str] = []

    print(f"[can-loopback-routing] sending {args.count} frames: "
          f"{args.tx_iface} -> (shared bus) -> {args.rx_iface}, "
          f"id=0x{can_id:X}, max_latency={args.max_latency_ms}ms")

    for seq in range(args.count):
        payload = seq.to_bytes(2, "big") + b"\xA5" * 6  # 8 bytes, sequence-tagged
        t_send = time.time()
        node.send_can(args.tx_iface, can_id, payload)

        msg = None
        deadline = t_send + (args.per_frame_timeout_ms / 1000.0)
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            m = bus.recv(timeout=remaining)
            if m is None:
                break
            if m.arbitration_id == can_id and bytes(m.data) == payload:
                msg = m
                break
            # Not our frame (unrelated bus traffic, or a stray late arrival
            # from an earlier sequence number) -- keep waiting within this
            # frame's own window.

        if msg is None:
            results.append((seq, payload, None, None))
            failures.append(f"seq {seq}: no matching frame observed on {args.rx_iface} "
                             f"within {args.per_frame_timeout_ms}ms")
        else:
            latency_ms = (msg.timestamp - t_send) * 1000.0
            recv_payload = bytes(msg.data)
            results.append((seq, payload, recv_payload, latency_ms))
            if recv_payload != payload:
                failures.append(f"seq {seq}: payload mismatch (sent {payload.hex()}, "
                                 f"got {recv_payload.hex()})")
            if latency_ms > args.max_latency_ms:
                failures.append(f"seq {seq}: latency {latency_ms:.3f}ms exceeds "
                                 f"{args.max_latency_ms}ms")
            if latency_ms < 0:
                failures.append(f"seq {seq}: negative latency {latency_ms:.3f}ms "
                                 f"(clock skew between send/receive timestamps?)")

        time.sleep(args.interval_ms / 1000.0)

    bus.shutdown()

    received = [r for r in results if r[3] is not None]
    latencies = [r[3] for r in received]

    print(f"[can-loopback-routing] {len(received)}/{args.count} frames received "
          f"({args.count - len(received)} dropped)")
    if latencies:
        latencies.sort()
        mean = sum(latencies) / len(latencies)
        p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]
        print(f"[can-loopback-routing] latency: min={latencies[0]:.3f}ms "
              f"max={latencies[-1]:.3f}ms mean={mean:.3f}ms p99={p99:.3f}ms")

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
