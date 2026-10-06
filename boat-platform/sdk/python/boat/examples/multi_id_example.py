# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Example: door-control-node — handle multiple CAN IDs in a single node.

Listens on vcan0 (iface_filter="vcan0") for three different IDs:
  0x300 — door open request:  sends ack 0x310, publishes door.state = "open"
  0x301 — door close request: sends ack 0x311, publishes door.state = "closed"
  0x302 — status query:       sends status reply 0x312 with current state byte

Demonstrates:
  - Handling multiple CAN IDs cleanly using a dispatch dict
  - Maintaining simple state across frames
  - Sending different response frames per ID
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.frame_node import FrameNode


class DoorControlNode:
    def __init__(self) -> None:
        self._frames = FrameNode()
        self._bus = BusNode(node_id="door-control")
        self._state = "closed"  # internal state persists across frames

        # Dispatch table: can_id → handler method
        self._handlers = {
            0x300: self._handle_open,
            0x301: self._handle_close,
            0x302: self._handle_status_query,
        }

    def on_frame(self, frame) -> None:
        handler = self._handlers.get(frame.can.can_id)
        if handler:
            handler(frame)

    def _handle_open(self, frame) -> None:
        self._state = "open"
        self._bus.publish("door.state", self._state)
        self._frames.send_can(frame.iface, 0x310, bytes([0x01]))  # ACK open

    def _handle_close(self, frame) -> None:
        self._state = "closed"
        self._bus.publish("door.state", self._state)
        self._frames.send_can(frame.iface, 0x311, bytes([0x00]))  # ACK close

    def _handle_status_query(self, frame) -> None:
        # Encode state as a single status byte: 0x01 = open, 0x00 = closed
        status = 0x01 if self._state == "open" else 0x00
        self._frames.send_can(frame.iface, 0x312, bytes([status]))

    def run(self) -> None:
        self._frames.subscribe(self.on_frame, bus_types=["CAN", "CANFD"],
                               iface_filter="vcan0")
        self._frames.run()


if __name__ == "__main__":
    DoorControlNode().run()
