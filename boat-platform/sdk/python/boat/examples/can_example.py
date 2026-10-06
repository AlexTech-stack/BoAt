# Copyright 2026 Alexander Günther
# SPDX-License-Identifier: Apache-2.0

"""Example CAN node: speed-limiter-alert.

Listens on vcan0 for CAN ID 0x100 (vehicle speed, 2 bytes big-endian, unit: 0.1 km/h).
When speed exceeds 120 km/h, sends an alert frame (ID 0x200, payload 0x01) back on
the same interface and publishes a 'speed.alert' boolean signal on the BoAt bus.

FrameNode is composed rather than subclassed: it takes a callback instead of
an on_frame() override. Frames arrive as unified boat.v1.Frame messages, so
the CAN id is frame.can.can_id and the bytes are frame.payload.
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.frame_node import FrameNode


class SpeedLimiterAlert:
    def __init__(self) -> None:
        # Addresses left unset so BOAT_HOST decides -- keeps the node portable.
        self._frames = FrameNode()
        # A BusNode is used alongside to publish signals independently of CAN.
        self._bus = BusNode(node_id="speed-alert")

    def on_frame(self, frame) -> None:
        if frame.can.can_id != 0x100:
            return
        # Decode speed: 2 bytes big-endian, unit 0.1 km/h
        speed_kmh = int.from_bytes(frame.payload[:2], "big") / 10.0
        alert = speed_kmh > 120.0
        # Publish boolean signal to the bus (always, so subscribers see updates)
        self._bus.publish("speed.alert", alert)
        if alert:
            # Send alert frame: ID 0x200, 1-byte payload 0x01
            self._frames.send_can(frame.iface, 0x200, bytes([0x01]))

    def run(self) -> None:
        self._frames.subscribe(self.on_frame, bus_types=["CAN", "CANFD"],
                               iface_filter="vcan0")
        self._frames.run()


if __name__ == "__main__":
    SpeedLimiterAlert().run()
