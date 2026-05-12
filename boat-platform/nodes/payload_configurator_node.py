#!/usr/bin/env python3
"""Payload configurator — set CAN and Ethernet payloads via the BoAt signal bus.

Publishes bus signals consumed by:
  cyclic_sender_node    → cyclic_sender.payload
  can_responder_node    → can_responder.payload
  eth_cyclic_sender_node → eth_cyclic_sender.payload

Commands (type at the interactive prompt):
  cyclic <hex>      — update cyclic_sender_node CAN payload      (max 8 bytes)
  responder <hex>   — update can_responder_node CAN payload      (max 8 bytes)
  eth <hex>         — update eth_cyclic_sender_node UDP payload   (any length)
  quit              — exit

Hex format: plain hex digits, with or without separators (: or space).
  Examples:  AABBCCDD   AA:BB:CC:DD   AA BB CC DD

Usage:
    python3 nodes/payload_configurator_node.py [--address localhost:50051]
"""

from __future__ import annotations

import argparse
import signal
import sys

from boat.bus_node import BusNode

_SIGNAL_CYCLIC    = "cyclic_sender.payload"
_SIGNAL_RESPONDER = "can_responder.payload"
_SIGNAL_ETH       = "eth_cyclic_sender.payload"

_ETH_MAX_PAYLOAD  = 1480  # 1500 - 20 (IP) - 8 (UDP) = max UDP data in one Ethernet frame

_HELP = """\
Commands:
  cyclic <hex>      set cyclic_sender_node CAN payload     e.g. cyclic AABB
  responder <hex>   set can_responder_node CAN payload     e.g. responder 1122334455667788
  eth <hex>         set eth_cyclic_sender_node UDP payload  e.g. eth 11AA22BB33CC
  quit              exit
"""


def _parse_hex(raw: str) -> bytes:
    """Accept hex with optional : or space separators."""
    cleaned = raw.replace(":", "").replace(" ", "")
    return bytes.fromhex(cleaned)


def main() -> None:
    parser = argparse.ArgumentParser(description="BoAt payload configurator")
    parser.add_argument("--address", default="localhost:50051")
    args = parser.parse_args()

    bus = BusNode(address=args.address, node_id="payload-configurator")

    def _shutdown(sig, frame) -> None:  # noqa: ANN001
        print("\n[configurator] shutting down…")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[configurator] connected to {args.address}")
    print(_HELP)

    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break

        if not line:
            continue

        if line in ("quit", "exit", "q"):
            break

        if line in ("help", "?"):
            print(_HELP)
            continue

        parts = line.split(None, 1)
        if len(parts) != 2:
            print("Usage: cyclic <hex> | responder <hex> | eth <hex>")
            continue

        cmd, hex_raw = parts

        try:
            payload = _parse_hex(hex_raw)
        except ValueError:
            print(f"  Invalid hex: {hex_raw!r}")
            continue

        if len(payload) == 0:
            print("  Payload must not be empty")
            continue

        if cmd == "cyclic":
            if len(payload) > 8:
                print(f"  Warning: CAN payload >8 bytes ({len(payload)}) — truncating to 8")
                payload = payload[:8]
            ok = bus.publish(_SIGNAL_CYCLIC, payload)
            status = "OK" if ok else "FAILED (gateway not reachable?)"
            print(f"  → {status}  cyclic_sender.payload = {payload.hex(':')}")

        elif cmd == "responder":
            if len(payload) > 8:
                print(f"  Warning: CAN payload >8 bytes ({len(payload)}) — truncating to 8")
                payload = payload[:8]
            ok = bus.publish(_SIGNAL_RESPONDER, payload)
            status = "OK" if ok else "FAILED (gateway not reachable?)"
            print(f"  → {status}  can_responder.payload = {payload.hex(':')}")

        elif cmd == "eth":
            if len(payload) > _ETH_MAX_PAYLOAD:
                print(f"  Warning: UDP payload >{_ETH_MAX_PAYLOAD} bytes ({len(payload)}) — truncating")
                payload = payload[:_ETH_MAX_PAYLOAD]
            ok = bus.publish(_SIGNAL_ETH, payload)
            status = "OK" if ok else "FAILED (gateway not reachable?)"
            print(f"  → {status}  eth_cyclic_sender.payload = {payload.hex(':')}")

        else:
            print(f"  Unknown command: {cmd!r}  (try 'cyclic', 'responder', or 'eth')")

    print("[configurator] bye")


if __name__ == "__main__":
    main()
