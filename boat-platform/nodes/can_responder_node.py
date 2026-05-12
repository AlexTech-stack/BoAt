#!/usr/bin/env python3
"""CAN responder node — Python equivalent of the C++ can_responder plugin.

Behaviour:
  - Listens for CAN ID 0x123 on vcan1
  - On each matching frame, sends 0x234 with the current payload on the same interface.
  - Bus signal ``can_responder.payload`` (bytes) updates the outgoing payload at any time.
    Default payload: 11:22:33:44:55:66:77:88

Usage:
    python3 nodes/can_responder_node.py [--address localhost:50051]
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading

from boat.bus_node import BusNode
from boat.can_node import CanNode

_LISTEN_ID       = 0x123
_LISTEN_IFACE    = "vcan1"
_RESPOND_ID      = 0x234
_RESPOND_PAYLOAD = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
_BUS_SIGNAL      = "can_responder.payload"


class _PayloadListener(BusNode):
    """Background bus subscriber that forwards payload updates via a callback."""

    def __init__(self, address: str, on_payload) -> None:
        super().__init__(address=address, node_id="can-responder-bus")
        self._on_payload = on_payload

    def on_signal(self, signal) -> None:
        if signal.name == _BUS_SIGNAL and signal.WhichOneof("value") == "bytes_value":
            self._on_payload(signal.bytes_value)


class CanResponderNode(CanNode):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._payload_lock = threading.Lock()
        self._payload = bytearray(_RESPOND_PAYLOAD)
        address = kwargs.get("address", "localhost:50051")
        self._bus = _PayloadListener(address=address, on_payload=self._update_payload)

    def _update_payload(self, data: bytes) -> None:
        with self._payload_lock:
            self._payload = bytearray(data)
        print(f"[responder] payload updated → {data.hex(':')}")

    def on_frame(self, frame, iface: str) -> None:
        if frame.can_id != _LISTEN_ID or iface != _LISTEN_IFACE:
            return
        with self._payload_lock:
            data = bytes(self._payload)
        print(
            f"[responder] received 0x{frame.can_id:03X} on {iface} "
            f"— sending 0x{_RESPOND_ID:03X} payload={data.hex(':')}"
        )
        self.send(_RESPOND_ID, data, iface=iface)

    def run(self) -> None:
        self._bus.run_background(names=[_BUS_SIGNAL])
        super().run()

    def stop(self) -> None:
        self._bus.stop()
        super().stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="BoAt CAN responder node (Python)")
    parser.add_argument("--address", default="localhost:50051", help="Gateway gRPC address")
    args = parser.parse_args()

    node = CanResponderNode(address=args.address, iface_filter="vcan1")

    def _shutdown(sig, frame) -> None:  # noqa: ANN001
        print("\n[responder] shutting down…")
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[responder] connected to {args.address}, listening for 0x{_LISTEN_ID:03X} on {_LISTEN_IFACE}")
    node.run()


if __name__ == "__main__":
    main()
